"""Tests for the stderr progress-bar helpers in __main__.py.

__main__.py is the root entry script, not a package module, so it is loaded
from its file path.  Importing it under a non-"__main__" name means the
`if __name__ == "__main__"` guard does not run main()."""

import importlib.util
import pathlib
import time

_spec = importlib.util.spec_from_file_location(
    "rv_main", pathlib.Path(__file__).resolve().parent.parent / "__main__.py"
)
rv_main = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rv_main)


class TestFmtDuration:
    def test_seconds(self):
        assert rv_main._fmt_duration(12.34) == "12.3s"

    def test_minutes(self):
        assert rv_main._fmt_duration(83) == "1m23s"

    def test_hours(self):
        assert rv_main._fmt_duration(3720) == "1h02m"


class TestRenderProgress:
    def test_full_bar_at_completion(self, capsys):
        rv_main._render_progress(100, 100, start=time.monotonic())
        err = capsys.readouterr().err
        assert err.startswith("\r")
        assert "100.0%" in err
        assert "#" * 30 in err          # fully filled bar
        assert "ETA 0.0s" in err

    def test_partial_bar_has_eta(self, capsys):
        rv_main._render_progress(50, 100, start=time.monotonic() - 10)
        err = capsys.readouterr().err
        assert " 50.0%" in err
        assert "#" * 15 in err          # half-filled bar
        assert "ETA" in err

    def test_zero_total_is_safe(self, capsys):
        # No work: treat as complete rather than dividing by zero.
        rv_main._render_progress(0, 0, start=time.monotonic())
        err = capsys.readouterr().err
        assert "100.0%" in err


class TestProcessChunkProgress:
    """_process_chunk reports its weight in per-block increments through a queue
    so the parent bar advances within a chunk, not only when the chunk ends."""

    def test_reports_per_block_weight(self):
        import queue
        chunk = (
            "\t.text\n"
            "\t.type f, @function\n"
            "f:\n\taddi a0, a0, 1\n\tret\n"
            "\t.type g, @function\n"
            "g:\n\taddi a1, a1, 2\n\tret\n"
        )
        q = queue.Queue()
        rv_main._process_chunk(chunk, False, rv_main.ScheduleMode.LIST, q, len(chunk))
        increments = []
        while not q.empty():
            increments.append(q.get())
        assert len(increments) == 2                       # one per non-empty block
        assert abs(sum(increments) - len(chunk)) < 1e-6   # increments sum to weight

    def test_no_queue_is_noop(self):
        # Backwards-compatible: omitting the queue must still work.
        chunk = "\t.type f, @function\nf:\n\taddi a0, a0, 1\n\tret\n"
        out = rv_main._process_chunk(chunk, False, rv_main.ScheduleMode.LIST)
        assert out and out[0][0] == "f"
