import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from lib.ui.log_reader import (
    DEFAULT_MAX_BYTES,
    DEFAULT_TAIL_LINES,
    MAX_READ_BYTES,
    MAX_TAIL_LINES,
    MIN_READ_BYTES,
    MIN_TAIL_LINES,
    BoundedLogReader,
    LogChangeKind,
    LogSnapshot,
    LogSnapshotState,
    classify_log_change,
)


class _BytesPath:
    def __fspath__(self):
        return b"bad"


class _EmptyPath:
    def __fspath__(self):
        return ""


class _ReadRecorder:
    def __init__(self, handle, sizes):
        self._handle = handle
        self._sizes = sizes

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self._handle.__exit__(*args)

    def __getattr__(self, name):
        return getattr(self._handle, name)

    def read(self, size=-1):
        self._sizes.append(size)
        return self._handle.read(size)


class _LateIoFailure:
    def __init__(self, handle, *, read_error=None, seek_error=None):
        self._handle = handle
        self._read_error = read_error
        self._seek_error = seek_error

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self._handle.__exit__(*args)

    def __getattr__(self, name):
        return getattr(self._handle, name)

    def read(self, size=-1):
        if self._read_error is not None:
            raise self._read_error
        return self._handle.read(size)

    def seek(self, *args):
        if self._seek_error is not None:
            raise self._seek_error
        return self._handle.seek(*args)


class BoundedLogReaderTests(unittest.TestCase):
    def setUp(self):
        self.reader = BoundedLogReader()

    def _write(self, root, name, data):
        path = Path(root) / name
        path.write_bytes(data)
        return path

    def test_constants_are_locked(self):
        self.assertEqual(MIN_TAIL_LINES, 1)
        self.assertEqual(DEFAULT_TAIL_LINES, 100)
        self.assertEqual(MAX_TAIL_LINES, 5000)
        self.assertEqual(MIN_READ_BYTES, 1024)
        self.assertEqual(DEFAULT_MAX_BYTES, 2 * 1024 * 1024)
        self.assertEqual(MAX_READ_BYTES, 8 * 1024 * 1024)

    def test_rejects_invalid_arguments(self):
        with tempfile.TemporaryDirectory() as root:
            path = self._write(root, "x.log", b"x")
            for value in (True, 1.5, "7"):
                with self.subTest(max_lines=value), self.assertRaises(TypeError):
                    self.reader.read(path, max_lines=value)
            for value in (0, MAX_TAIL_LINES + 1):
                with self.subTest(max_lines=value), self.assertRaises(ValueError):
                    self.reader.read(path, max_lines=value)
            for value in (False, 1.5, "1024"):
                with self.subTest(max_bytes=value), self.assertRaises(TypeError):
                    self.reader.read(path, max_bytes=value)
            for value in (MIN_READ_BYTES - 1, MAX_READ_BYTES + 1):
                with self.subTest(max_bytes=value), self.assertRaises(ValueError):
                    self.reader.read(path, max_bytes=value)

    def test_rejects_empty_and_bytes_paths_before_path_construction(self):
        for value, error in (("", ValueError), (_EmptyPath(), ValueError), (b"x", TypeError), (_BytesPath(), TypeError), (object(), TypeError)):
            with self.subTest(value=value), self.assertRaises(error):
                self.reader.read(value)

    def test_whitespace_and_relative_paths_are_valid_and_canonical(self):
        with tempfile.TemporaryDirectory() as root:
            old = os.getcwd()
            try:
                os.chdir(root)
                Path(" spaced").write_text("x", encoding="utf-8")
                snap = self.reader.read(" spaced")
                self.assertEqual(snap.path, str(Path(" spaced").resolve()))
                self.assertEqual(snap.lines, ("x",))
            finally:
                os.chdir(old)

    def test_missing_empty_and_newline_only_states(self):
        with tempfile.TemporaryDirectory() as root:
            missing = self.reader.read(Path(root) / "missing.log")
            self.assertEqual(missing.state, LogSnapshotState.MISSING)
            self.assertEqual(missing.lines, ())
            self.assertIsNone(missing.error)
            empty_path = self._write(root, "empty.log", b"")
            empty = self.reader.read(empty_path)
            self.assertEqual(empty.state, LogSnapshotState.EMPTY)
            self.assertEqual(empty.size_bytes, 0)
            self.assertIsNotNone(empty.file_identity)
            newline = self.reader.read(self._write(root, "newline.log", b"\n"))
            self.assertEqual(newline.state, LogSnapshotState.READY)
            self.assertEqual(newline.lines, ("",))

    def test_line_endings_final_newline_and_line_limits(self):
        with tempfile.TemporaryDirectory() as root:
            cases = {
                "no-final.log": (b"one", ("one",)),
                "final.log": (b"one\n", ("one",)),
                "crlf.log": (b"one\r\ntwo\r\n", ("one", "two")),
                "lf.log": (b"one\ntwo\n", ("one", "two")),
            }
            for name, (raw, expected) in cases.items():
                with self.subTest(name=name):
                    self.assertEqual(self.reader.read(self._write(root, name, raw)).lines, expected)
            path = self._write(root, "many.log", b"1\n2\n3\n4\n")
            snap = self.reader.read(path, max_lines=2)
            self.assertEqual(snap.lines, ("3", "4"))
            self.assertTrue(snap.truncated)
            exact = self.reader.read(path, max_lines=4)
            self.assertEqual(exact.lines, ("1", "2", "3", "4"))

    def test_byte_window_boundary_rules_and_long_line_suffixes(self):
        with tempfile.TemporaryDirectory() as root:
            # max_bytes minimum is 1024; make deterministic windows around that size.
            first = b"A" * 1023 + b"\n" + b"first\nsecond\n"
            snap = self.reader.read(self._write(root, "boundary.log", first), max_bytes=1024)
            self.assertEqual(snap.lines[-2:], ("first", "second"))
            self.assertNotIn("A", "".join(snap.lines))

            partial = b"P" * 100 + b"\ncomplete\nlast"
            raw = b"X" * (1200 - len(partial)) + partial
            snap = self.reader.read(self._write(root, "partial.log", raw), max_bytes=1024)
            self.assertEqual(snap.lines[-2:], ("complete", "last"))

            no_lf = b"Z" * 2000
            snap = self.reader.read(self._write(root, "long.log", no_lf), max_bytes=1024)
            self.assertEqual(snap.lines, ("Z" * 1024,))
            self.assertTrue(snap.truncated)

            final_lf = b"Q" * 2000 + b"\n"
            snap = self.reader.read(self._write(root, "long-final.log", final_lf), max_bytes=1024)
            self.assertEqual(snap.lines, ("Q" * 1023,))

    def test_utf8_boundary_invalid_bytes_tabs_and_long_text_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            prefix = ("é" * 700).encode("utf-8") + b"\n"
            raw = prefix + "日本語\tkeep\npartial✓".encode("utf-8")
            snap = self.reader.read(self._write(root, "utf8.log", raw), max_bytes=1024)
            self.assertEqual(snap.lines[-2:], ("日本語\tkeep", "partial✓"))
            self.assertNotIn("�", snap.lines[-2])
            invalid = self.reader.read(self._write(root, "invalid.log", b"ok\xffbad\n"))
            self.assertEqual(invalid.lines, ("ok�bad",))
            long_line = "x\t" + "y" * 5000
            preserved = self.reader.read(self._write(root, "preserve.log", long_line.encode()), max_bytes=8192)
            self.assertEqual(preserved.lines, (long_line,))

    def test_identity_size_unreadable_and_bounded_reads(self):
        with tempfile.TemporaryDirectory() as root:
            path = self._write(root, "read.log", b"a\n" * 1000)
            sizes = []
            def opener(p, mode):
                return _ReadRecorder(open(p, mode), sizes)
            snap = BoundedLogReader(opener=opener).read(path, max_lines=100, max_bytes=1024)
            self.assertEqual(snap.size_bytes, path.stat().st_size)
            self.assertIsNotNone(snap.file_identity)
            self.assertTrue(sizes)
            self.assertNotIn(-1, sizes)
            self.assertTrue(all(0 <= size <= 1024 for size in sizes))
            directory = self.reader.read(Path(root))
            self.assertEqual(directory.state, LogSnapshotState.UNREADABLE)
            self.assertTrue(directory.error.startswith(("PermissionError:", "IsADirectoryError:")))
            def denied_opener(*args, **kwargs):
                raise PermissionError("denied")
            unreadable = BoundedLogReader(opener=denied_opener).read(path)
            self.assertEqual(unreadable.state, LogSnapshotState.UNREADABLE)
            self.assertEqual(unreadable.error, "PermissionError: denied")

    def _assert_late_io_snapshot(self, snapshot, path, error_type):
        stat = path.stat()
        expected_identity = (
            (int(stat.st_dev), int(stat.st_ino))
            if stat.st_dev or stat.st_ino
            else None
        )
        self.assertEqual(snapshot.state, LogSnapshotState.UNREADABLE)
        self.assertEqual(snapshot.size_bytes, stat.st_size)
        self.assertEqual(snapshot.file_identity, expected_identity)
        self.assertTrue(snapshot.error.startswith(f"{error_type}:"))

    def test_post_fstat_read_file_not_found_is_unreadable_with_metadata(self):
        with tempfile.TemporaryDirectory() as root:
            path = self._write(root, "late-missing.log", b"abc\n")

            def opener(value, mode):
                return _LateIoFailure(
                    open(value, mode),
                    read_error=FileNotFoundError("late missing"),
                )

            snapshot = BoundedLogReader(opener=opener).read(path)
            self._assert_late_io_snapshot(snapshot, path, "FileNotFoundError")

    def test_post_fstat_read_os_error_is_unreadable_with_metadata(self):
        with tempfile.TemporaryDirectory() as root:
            path = self._write(root, "late-read.log", b"abc\n")

            def opener(value, mode):
                return _LateIoFailure(
                    open(value, mode),
                    read_error=OSError("late read"),
                )

            snapshot = BoundedLogReader(opener=opener).read(path)
            self._assert_late_io_snapshot(snapshot, path, "OSError")

    def test_post_fstat_seek_os_error_is_unreadable_with_metadata(self):
        with tempfile.TemporaryDirectory() as root:
            path = self._write(root, "late-seek.log", b"abc\n")

            def opener(value, mode):
                return _LateIoFailure(
                    open(value, mode),
                    seek_error=OSError("late seek"),
                )

            snapshot = BoundedLogReader(opener=opener).read(path)
            self._assert_late_io_snapshot(snapshot, path, "OSError")

    def test_initial_opener_file_not_found_remains_missing(self):
        def missing_opener(*args, **kwargs):
            raise FileNotFoundError("initial missing")

        snapshot = BoundedLogReader(opener=missing_opener).read("missing.log")
        self.assertEqual(snapshot.state, LogSnapshotState.MISSING)
        self.assertEqual(snapshot.size_bytes, 0)
        self.assertIsNone(snapshot.file_identity)
        self.assertIsNone(snapshot.error)

    def test_exact_5000_line_boundary(self):
        with tempfile.TemporaryDirectory() as root:
            prefix = b"x" * 2048 + b"\n"
            payload = b"".join(f"line-{i:04d}\n".encode() for i in range(5000))
            snap = self.reader.read(self._write(root, "5000.log", prefix + payload), max_lines=5000, max_bytes=len(payload))
            self.assertEqual(len(snap.lines), 5000)
            self.assertEqual(snap.lines[0], "line-0000")
            self.assertEqual(snap.lines[-1], "line-4999")


class LogChangeClassificationTests(unittest.TestCase):
    def snap(self, state=LogSnapshotState.READY, **kwargs):
        defaults = dict(path="x", state=state, lines=("x",), size_bytes=1, file_identity=(1, 1), truncated=False, error=None)
        defaults.update(kwargs)
        return LogSnapshot(**defaults)

    def test_initial_unchanged_append_truncate_rotate(self):
        base = self.snap()
        self.assertEqual(classify_log_change(None, base), LogChangeKind.INITIAL)
        self.assertEqual(classify_log_change(base, base), LogChangeKind.UNCHANGED)
        self.assertEqual(classify_log_change(base, replace(base, size_bytes=2)), LogChangeKind.APPENDED)
        self.assertEqual(classify_log_change(base, replace(base, size_bytes=0)), LogChangeKind.TRUNCATED)
        self.assertEqual(classify_log_change(base, replace(base, file_identity=(1, 2))), LogChangeKind.ROTATED)

    def test_missing_reappeared_unreadable_and_state_changes(self):
        ready = self.snap()
        missing = self.snap(LogSnapshotState.MISSING, lines=(), size_bytes=0, file_identity=None)
        unreadable = self.snap(LogSnapshotState.UNREADABLE, lines=(), error="PermissionError: x")
        self.assertEqual(classify_log_change(ready, missing), LogChangeKind.BECAME_MISSING)
        self.assertEqual(classify_log_change(missing, ready), LogChangeKind.REAPPEARED)
        self.assertEqual(classify_log_change(ready, unreadable), LogChangeKind.STATE_CHANGED)
        self.assertEqual(classify_log_change(unreadable, ready), LogChangeKind.STATE_CHANGED)
        self.assertEqual(classify_log_change(unreadable, unreadable), LogChangeKind.UNCHANGED)
        self.assertEqual(classify_log_change(unreadable, replace(unreadable, error="OSError: y")), LogChangeKind.STATE_CHANGED)

    def test_empty_ready_directional_and_unknown_identity(self):
        empty = self.snap(LogSnapshotState.EMPTY, lines=(), size_bytes=0)
        ready = self.snap(size_bytes=4)
        self.assertEqual(classify_log_change(empty, ready), LogChangeKind.APPENDED)
        self.assertEqual(classify_log_change(ready, empty), LogChangeKind.TRUNCATED)
        weird_ready = replace(ready, size_bytes=0)
        self.assertEqual(classify_log_change(empty, weird_ready), LogChangeKind.STATE_CHANGED)
        unknown1 = replace(ready, file_identity=None)
        self.assertEqual(classify_log_change(unknown1, replace(unknown1, size_bytes=5)), LogChangeKind.APPENDED)
        self.assertEqual(classify_log_change(unknown1, replace(unknown1, lines=("y",))), LogChangeKind.STATE_CHANGED)


if __name__ == "__main__":
    unittest.main()
