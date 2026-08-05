"""Tests for the time-series logger."""

import csv
import json
import os
import shutil
import tempfile
import threading
import time
import unittest

from core import sampling

FIELDS = ["pack_v", "cell1_v"]


class LoggerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="obi-test-")
        self.path = os.path.join(self.tmp, "log.csv")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def read_csv(self, path=None):
        with open(path or self.path, newline="") as handle:
            return list(csv.DictReader(handle))

    def run_logger(self, sample_fn, **kwargs):
        kwargs.setdefault("interval", 0.01)
        kwargs.setdefault("max_samples", 3)
        fmt = kwargs.pop("fmt", "csv")
        append = kwargs.pop("append", False)
        path = kwargs.pop("path", self.path)
        writer = sampling.make_writer(path, FIELDS, fmt=fmt, append=append)
        logger = sampling.SampleLogger(sample_fn, writer, **kwargs)
        logger.start()
        logger.join(timeout=10)
        self.assertFalse(logger.is_running, "logger did not finish in time")
        return logger


class CsvLoggingTest(LoggerTestCase):
    def test_writes_a_header_and_one_row_per_sample(self):
        logger = self.run_logger(lambda: {"pack_v": 18.0, "cell1_v": 3.6})

        rows = self.read_csv()
        self.assertEqual(len(rows), 3)
        self.assertEqual(logger.samples, 3)
        self.assertEqual(logger.errors, 0)
        self.assertEqual(list(rows[0].keys()),
                         ["timestamp", "elapsed_s", "sample", "pack_v", "cell1_v", "error"])
        self.assertEqual(rows[0]["pack_v"], "18.0")
        self.assertEqual([row["sample"] for row in rows], ["1", "2", "3"])
        self.assertEqual(rows[0]["error"], "")

    def test_timestamps_are_iso8601_with_offset(self):
        self.run_logger(lambda: {"pack_v": 1}, max_samples=1)

        stamp = self.read_csv()[0]["timestamp"]
        self.assertRegex(stamp, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")

    def test_failed_samples_become_visible_error_rows(self):
        state = {"calls": 0}

        def flaky():
            state["calls"] += 1
            if state["calls"] == 2:
                raise ConnectionError("battery removed")
            return {"pack_v": 18.0, "cell1_v": 3.6}

        logger = self.run_logger(flaky)

        rows = self.read_csv()
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1]["pack_v"], "")
        self.assertIn("battery removed", rows[1]["error"])
        self.assertEqual(logger.samples, 2)
        self.assertEqual(logger.errors, 1)

    def test_unknown_keys_do_not_break_the_writer(self):
        self.run_logger(lambda: {"pack_v": 18.0, "surprise": 1}, max_samples=1)

        rows = self.read_csv()
        self.assertNotIn("surprise", rows[0])
        self.assertEqual(rows[0]["pack_v"], "18.0")

    def test_append_keeps_previous_rows_and_writes_one_header(self):
        self.run_logger(lambda: {"pack_v": 1}, max_samples=2)
        self.run_logger(lambda: {"pack_v": 2}, max_samples=2, append=True)

        with open(self.path) as handle:
            content = handle.read()
        self.assertEqual(content.count("timestamp"), 1)
        self.assertEqual(len(self.read_csv()), 4)

    def test_appending_with_different_columns_is_refused(self):
        self.run_logger(lambda: {"pack_v": 1}, max_samples=1)

        # Same file, but now logging an extra column: appending would put the
        # values under the wrong headings.
        writer = sampling.make_writer(self.path, FIELDS + ["charge_count"], append=True)
        with self.assertRaises(ValueError) as caught:
            writer.open()

        self.assertIn("different columns", str(caught.exception))
        # The original log must be untouched.
        self.assertEqual(len(self.read_csv()), 1)

    def test_appending_with_the_same_columns_is_allowed(self):
        self.run_logger(lambda: {"pack_v": 1}, max_samples=1)
        writer = sampling.make_writer(self.path, FIELDS, append=True)
        writer.open()
        writer.close()

    def test_missing_directories_are_created(self):
        nested = os.path.join(self.tmp, "a", "b", "log.csv")
        self.run_logger(lambda: {"pack_v": 1}, max_samples=1, path=nested)

        self.assertTrue(os.path.exists(nested))


class JsonlLoggingTest(LoggerTestCase):
    def test_writes_one_json_object_per_line(self):
        path = os.path.join(self.tmp, "log.jsonl")
        self.run_logger(lambda: {"pack_v": 18.0}, max_samples=2, fmt="jsonl", path=path)

        with open(path) as handle:
            rows = [json.loads(line) for line in handle if line.strip()]

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["pack_v"], 18.0)
        self.assertEqual(rows[0]["sample"], 1)


class StopConditionsTest(LoggerTestCase):
    def test_stop_ends_the_run_promptly(self):
        started = threading.Event()

        def sample():
            started.set()
            return {"pack_v": 1}

        writer = sampling.make_writer(self.path, FIELDS)
        logger = sampling.SampleLogger(sample, writer, interval=60)
        logger.start()
        self.assertTrue(started.wait(5))

        begin = time.monotonic()
        logger.stop()

        self.assertLess(time.monotonic() - begin, 5,
                        "stop() should interrupt the sleep, not wait for the interval")
        self.assertFalse(logger.is_running)

    def test_duration_limit_finishes_the_run(self):
        writer = sampling.make_writer(self.path, FIELDS)
        logger = sampling.SampleLogger(lambda: {"pack_v": 1}, writer,
                                       interval=0.01, max_duration=0.05)
        logger.start()
        logger.join(timeout=10)

        self.assertFalse(logger.is_running)
        self.assertGreaterEqual(logger.samples, 1)

    def test_consecutive_errors_can_stop_the_run(self):
        events = []

        def always_fails():
            raise ConnectionError("adapter unplugged")

        writer = sampling.make_writer(self.path, FIELDS)
        logger = sampling.SampleLogger(always_fails, writer, interval=0.01,
                                       stop_after_errors=3, on_event=events.append)
        logger.start()
        logger.join(timeout=10)

        self.assertEqual(logger.errors, 3)
        finished = [event for event in events if event["type"] == "finished"]
        self.assertEqual(finished[-1]["reason"], "too-many-errors")

    def test_write_failure_stops_and_reports(self):
        class ExplodingWriter(object):
            path = "/nowhere/log.csv"

            def open(self):
                pass

            def write(self, row):
                raise OSError(28, "No space left on device")

            def close(self):
                pass

        events = []
        logger = sampling.SampleLogger(lambda: {"pack_v": 1}, ExplodingWriter(),
                                       interval=0.01, on_event=events.append)
        logger.start()
        logger.join(timeout=10)

        finished = [event for event in events if event["type"] == "finished"]
        self.assertEqual(finished[-1]["reason"], "write-failed")
        self.assertIn("No space left", logger.last_error)

    def test_a_broken_event_consumer_does_not_kill_the_logger(self):
        def bad_consumer(event):
            raise RuntimeError("UI exploded")

        writer = sampling.make_writer(self.path, FIELDS)
        logger = sampling.SampleLogger(lambda: {"pack_v": 1}, writer,
                                       interval=0.01, max_samples=2,
                                       on_event=bad_consumer)
        logger.start()
        logger.join(timeout=10)

        self.assertEqual(logger.samples, 2)


class SchedulingTest(unittest.TestCase):
    def test_missed_slots_are_skipped_instead_of_bursting(self):
        writer = type("NullWriter", (), {"path": "-", "open": lambda self: None,
                                         "write": lambda self, row: None,
                                         "close": lambda self: None})()
        logger = sampling.SampleLogger(lambda: {}, writer, interval=1.0)
        logger.started_at = time.monotonic() - 10.0  # pretend sampling took 10 s

        begin = time.monotonic()
        logger._stop.set()  # so the wait returns immediately
        logger._wait_for_next(index=1)

        self.assertLess(time.monotonic() - begin, 1.0)


class PathTest(unittest.TestCase):
    def test_default_path_is_timestamped_and_under_the_xdg_dir(self):
        from datetime import datetime
        when = datetime(2026, 8, 5, 13, 30, 0)

        path = sampling.default_log_path("makita-lxt", "csv", when=when)

        self.assertTrue(path.endswith("makita-lxt-20260805-133000.csv"))
        self.assertIn("obi-linux", path)

    def test_unknown_format_is_rejected(self):
        with self.assertRaises(ValueError):
            sampling.make_writer("/tmp/x.txt", FIELDS, fmt="xml")


if __name__ == "__main__":
    unittest.main()
