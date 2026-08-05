"""Periodic sampling to CSV/JSONL, used for logging a battery over time.

The logger runs on its own thread and is deliberately dumb about *what* it is
sampling: it calls a function, writes whatever dictionary comes back, and
keeps going. Failed samples are written as rows with an ``error`` column so a
gap in the data is visible instead of silently missing.
"""

import csv
import json
import os
import threading
import time
from datetime import datetime

DEFAULT_INTERVAL = 10.0
#: Columns every log starts with, whatever is being sampled.
BASE_FIELDS = ["timestamp", "elapsed_s", "sample"]
#: Column every log ends with.
ERROR_FIELD = "error"


def data_dir():
    """XDG data directory for OBI Linux."""
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "obi-linux")


def log_dir():
    return os.path.join(data_dir(), "logs")


def default_log_path(prefix="obi", fmt="csv", directory=None, when=None):
    """Build a timestamped log path, e.g. ``.../makita-lxt-20260805-133000.csv``."""
    when = when or datetime.now()
    name = "%s-%s.%s" % (prefix, when.strftime("%Y%m%d-%H%M%S"), fmt)
    return os.path.join(directory or log_dir(), name)


def timestamp():
    """Local time with UTC offset, e.g. ``2026-08-05T13:30:00+02:00``."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


class _BaseWriter(object):
    def __init__(self, path, fieldnames, append=False):
        self.path = path
        self.fieldnames = list(BASE_FIELDS) + list(fieldnames) + [ERROR_FIELD]
        self.append = append
        self._handle = None

    def _open_handle(self, newline=""):
        directory = os.path.dirname(os.path.abspath(self.path))
        if directory:
            try:
                os.makedirs(directory)
            except OSError:
                if not os.path.isdir(directory):
                    raise
        exists = os.path.exists(self.path) and os.path.getsize(self.path) > 0
        mode = "a" if (self.append and exists) else "w"
        self._handle = open(self.path, mode, newline=newline)
        return mode == "a"

    def close(self):
        if self._handle is None:
            return
        try:
            self._handle.flush()
            os.fsync(self._handle.fileno())
        except (OSError, ValueError):  # pragma: no cover - stream already closed
            pass
        finally:
            self._handle.close()
            self._handle = None


class CsvSampleWriter(_BaseWriter):
    """Append rows to a CSV file, flushing after every row."""

    def open(self):
        self._check_existing_header()
        appending = self._open_handle(newline="")
        self._writer = csv.DictWriter(
            self._handle, fieldnames=self.fieldnames,
            extrasaction="ignore", restval="")
        if not appending:
            self._writer.writeheader()
            self._handle.flush()

    def _check_existing_header(self):
        """Refuse to append rows that do not match the file's columns.

        Without this, appending a log with different options (say, after
        turning on the status columns) would write values under the wrong
        headings - the resulting file looks fine and is quietly wrong.
        """
        if not self.append or not os.path.exists(self.path):
            return
        if os.path.getsize(self.path) == 0:
            return
        with open(self.path, newline="") as handle:
            existing = next(csv.reader(handle), None)
        if existing and existing != self.fieldnames:
            raise ValueError(
                "%s already exists with different columns.\n\n"
                "In the file : %s\n"
                "Now logging : %s\n\n"
                "Write to a different file, or use the same options as the existing log."
                % (self.path, ", ".join(existing), ", ".join(self.fieldnames)))

    def write(self, row):
        self._writer.writerow({k: ("" if v is None else v) for k, v in row.items()})
        self._handle.flush()


class JsonlSampleWriter(_BaseWriter):
    """One JSON object per line; handy for feeding other tooling."""

    def open(self):
        self._open_handle(newline="\n")

    def write(self, row):
        ordered = {}
        for field in self.fieldnames:
            if field in row:
                ordered[field] = row[field]
        for key, value in row.items():
            if key not in ordered:
                ordered[key] = value
        self._handle.write(json.dumps(ordered) + "\n")
        self._handle.flush()


def make_writer(path, fieldnames, fmt="csv", append=False):
    if fmt == "jsonl":
        return JsonlSampleWriter(path, fieldnames, append=append)
    if fmt == "csv":
        return CsvSampleWriter(path, fieldnames, append=append)
    raise ValueError("Unknown log format: %r" % (fmt,))


class SampleLogger(object):
    """Call ``sample_fn`` every ``interval`` seconds and write the result.

    ``on_event`` is invoked from the logger thread with dictionaries of the
    form ``{"type": "sample"|"error"|"finished", ...}``. GUI consumers must
    marshal those onto their own thread.
    """

    def __init__(self, sample_fn, writer, interval=DEFAULT_INTERVAL,
                 max_samples=None, max_duration=None, on_event=None,
                 stop_after_errors=None, name="obi-logger"):
        if interval <= 0:
            raise ValueError("Interval must be positive")
        self.sample_fn = sample_fn
        self.writer = writer
        self.interval = float(interval)
        self.max_samples = max_samples
        self.max_duration = max_duration
        self.on_event = on_event
        self.stop_after_errors = stop_after_errors
        self.name = name

        self.samples = 0
        self.errors = 0
        self.consecutive_errors = 0
        self.last_error = None
        self.last_row = None
        self.started_at = None

        self._stop = threading.Event()
        self._thread = None

    # ------------------------------------------------------------------
    @property
    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    @property
    def path(self):
        return self.writer.path

    def start(self):
        """Open the log file and start sampling.

        The file is opened on the calling thread so that a bad path or a
        read-only directory surfaces immediately instead of disappearing into
        a background thread.
        """
        if self.is_running:
            raise RuntimeError("Logger is already running")
        self.writer.open()
        self._stop.clear()
        self.started_at = time.monotonic()
        self._thread = threading.Thread(target=self._run, name=self.name)
        self._thread.daemon = True
        self._thread.start()

    def stop(self, wait=True, timeout=10.0):
        self._stop.set()
        if wait and self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout)

    def join(self, timeout=None):
        if self._thread is not None:
            self._thread.join(timeout)

    # ------------------------------------------------------------------
    def _emit(self, event):
        callback = self.on_event
        if callback is None:
            return
        try:
            callback(event)
        except Exception:  # pragma: no cover - a broken consumer must not stop logging
            pass

    def _run(self):
        index = 0
        stop_reason = "stopped"
        try:
            while not self._stop.is_set():
                index += 1
                now = time.monotonic()
                row = {
                    "timestamp": timestamp(),
                    "elapsed_s": round(now - self.started_at, 3),
                    "sample": index,
                }
                try:
                    data = self.sample_fn()
                    if data:
                        row.update(data)
                    self.samples += 1
                    self.consecutive_errors = 0
                except Exception as exc:
                    message = "%s: %s" % (type(exc).__name__, exc)
                    row[ERROR_FIELD] = message
                    self.errors += 1
                    self.consecutive_errors += 1
                    self.last_error = message
                    self._emit({"type": "error", "index": index, "message": message})

                try:
                    self.writer.write(row)
                except Exception as exc:  # disk full, removable media gone, ...
                    self.last_error = "Write failed: %s: %s" % (type(exc).__name__, exc)
                    self._emit({"type": "error", "index": index, "message": self.last_error})
                    stop_reason = "write-failed"
                    break

                self.last_row = row
                self._emit({"type": "sample", "index": index, "row": row})

                if (self.stop_after_errors is not None
                        and self.consecutive_errors >= self.stop_after_errors):
                    stop_reason = "too-many-errors"
                    break
                if self.max_samples is not None and index >= self.max_samples:
                    stop_reason = "sample-limit"
                    break
                if (self.max_duration is not None
                        and time.monotonic() - self.started_at >= self.max_duration):
                    stop_reason = "duration-limit"
                    break

                if not self._wait_for_next(index):
                    stop_reason = "stopped"
                    break
        finally:
            try:
                self.writer.close()
            except Exception:  # pragma: no cover - best effort
                pass
            self._emit({
                "type": "finished",
                "reason": stop_reason,
                "samples": self.samples,
                "errors": self.errors,
                "path": self.writer.path,
            })

    def _wait_for_next(self, index):
        """Sleep until the next slot. Returns False when asked to stop.

        Deadlines are absolute so the schedule does not drift, and slots that
        were missed because a sample took too long are skipped rather than
        fired back to back.
        """
        deadline = self.started_at + index * self.interval
        now = time.monotonic()
        while deadline <= now:
            deadline += self.interval
        return not self._stop.wait(deadline - now)
